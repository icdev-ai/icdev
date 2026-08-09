#!/usr/bin/env python3
# CUI // SP-CTI
"""Hybrid search: combines BM25 keyword search + semantic vector search.

Supports user-scoped queries (D180), time-decay ranking (D147), and JSON output.
"""

import argparse
import json
import sqlite3
import struct
import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.db.storage import StorageConnection, get_connection
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Overridable in tests via monkeypatch
DB_PATH = None

_CLASSIFICATION_ORDER = {
    "PUBLIC": 0,
    "CUI": 1,
    "SECRET": 2,
    "TOP SECRET": 3,
    "TOP SECRET//SCI": 4,
}


def _classification_level(label: str) -> int:
    return _CLASSIFICATION_ORDER.get((label or "CUI").upper().strip(), 1)


def _compartments_allowed(entry_compartments: str, user_compartments: list[str] | None) -> bool:
    if not entry_compartments or entry_compartments.strip() == "":
        return True
    if user_compartments is None:
        return False
    entry_set = {c.strip().upper() for c in entry_compartments.split(",") if c.strip()}
    user_set = {c.strip().upper() for c in user_compartments if c.strip()}
    return entry_set <= user_set


def _connect():
    if DB_PATH is not None:
        # DB_PATH is a stand-in for get_connection(), which returns a
        # StorageConnection that rewrites PostgreSQL ``%s`` placeholders to
        # ``?`` for SQLite. Returning the bare sqlite3 connection made the seam
        # lie: every parameterised statement raised ``near "%": syntax error``,
        # and callers that swallow write failures reported a no-op as success.
        return StorageConnection(sqlite3.connect(str(DB_PATH)), "sqlite")
    return get_connection()


def get_all_entries(user_id=None, tenant_id=None, clearance=None, compartments=None):
    conn = _connect()
    c = conn.cursor()

    sql = "SELECT id, content, type, importance, embedding, created_at, classification, compartment FROM memory_entries WHERE 1=1"
    params = []

    if user_id:
        sql += " AND (user_id = ? OR user_id IS NULL)"
        params.append(user_id)
    if tenant_id:
        sql += " AND (tenant_id = ? OR tenant_id IS NULL)"
        params.append(tenant_id)

    c.execute(sql, params)
    rows = c.fetchall()
    conn.close()

    # Security-context filtering
    if clearance is not None:
        user_level = _classification_level(clearance)
        filtered = []
        for row in rows:
            entry_class = row[6] if len(row) > 6 else "CUI"
            entry_compartment = row[7] if len(row) > 7 else ""
            if _classification_level(entry_class) <= user_level and _compartments_allowed(entry_compartment, compartments):
                filtered.append(row)
        rows = filtered

    return rows


def fts5_search(query: str, limit: int = 20) -> list[dict]:
    """FTS5 full-text search backend (adapt-hermes-03).

    Runs when query has >2 tokens and SQLite FTS5 is available.
    Returns normalized result dicts compatible with hybrid_rank input.
    Falls back to [] gracefully on PostgreSQL or if memory_fts doesn't exist.
    Weight in final score: 0.3 (blended with BM25 0.7 + semantic 0.3 → 1.0 total).
    """
    if len(query.split()) <= 2:
        return []
    try:
        from tools.memory.session_indexer import search_history
        return search_history(query, limit=limit)
    except Exception:
        return []


def bm25_search(query, entries):
    """BM25 keyword ranking with fallback to simple term frequency."""
    documents = [entry[1] for entry in entries]

    try:
        from rank_bm25 import BM25Okapi

        tokenized = [doc.lower().split() for doc in documents]
        bm25 = BM25Okapi(tokenized)
        scores = bm25.get_scores(query.lower().split())
    except ImportError:
        # Fallback: simple term frequency
        query_terms = query.lower().split()
        scores = []
        for doc in documents:
            doc_lower = doc.lower()
            score = sum(doc_lower.count(term) for term in query_terms)
            scores.append(float(score))

    # Normalize scores to 0-1
    max_score = float(max(scores)) if len(scores) > 0 and float(max(scores)) > 0 else 1.0
    return [s / max_score for s in scores]


def semantic_search(query, entries):
    """Semantic similarity using embeddings (vendor-agnostic via LLM provider)."""
    # Try LLM provider system first (supports OpenAI, Ollama, Bedrock Titan)
    query_emb = None
    try:
        from tools.llm import get_embedding_provider

        provider = get_embedding_provider()
        query_emb = provider.embed(query)
    except Exception:
        pass

    # Fallback to direct OpenAI
    if query_emb is None:
        try:
            from dotenv import load_dotenv

            load_dotenv(BASE_DIR / ".env")
        except ImportError:
            pass

        import os

        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            return None

        try:
            import openai

            client = openai.OpenAI(api_key=api_key)
            response = client.embeddings.create(input=query, model="text-embedding-3-small")
            query_emb = response.data[0].embedding
        except (ImportError, Exception):
            return None

    if query_emb is None:
        return None

    scores = []
    for entry in entries:
        emb_blob = entry[4]
        if emb_blob is None:
            scores.append(0.0)
            continue
        n = len(emb_blob) // 4
        stored_emb = list(struct.unpack(f"{n}f", emb_blob))

        # Cosine similarity
        try:
            import numpy as np

            a, b = np.array(query_emb), np.array(stored_emb)
            score = float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))
        except ImportError:
            dot = sum(x * y for x, y in zip(query_emb, stored_emb))
            norm_a = sum(x * x for x in query_emb) ** 0.5
            norm_b = sum(x * x for x in stored_emb) ** 0.5
            score = dot / (norm_a * norm_b) if norm_a and norm_b else 0.0

        scores.append(score)

    # Normalize to 0-1
    max_score = max(scores) if scores and max(scores) > 0 else 1.0
    return [s / max_score for s in scores]


def hybrid_rank(
    entries, bm25_scores, semantic_scores, bm25_weight, semantic_weight,
    time_decay_enabled=False, decay_config=None, classification_penalty=False
):
    """Combine BM25 and semantic scores, with optional time-decay (D147) and classification penalty."""
    results = []
    for i, entry in enumerate(entries):
        bm25_s = bm25_scores[i] if bm25_scores else 0.0
        sem_s = semantic_scores[i] if semantic_scores else 0.0

        if semantic_scores is None:
            # No semantic available, use BM25 only
            combined = bm25_s
        else:
            combined = (bm25_weight * bm25_s) + (semantic_weight * sem_s)

        id_, content, type_, importance, _, created_at, classification, compartment = entry

        # D147: Apply time-decay reranking when enabled
        if time_decay_enabled:
            try:
                from tools.memory.time_decay import compute_time_aware_score

                combined = compute_time_aware_score(
                    base_score=combined,
                    created_at=created_at or "",
                    memory_type=type_ or "event",
                    importance=importance or 5,
                    config=decay_config,
                )
            except (ImportError, Exception):
                pass  # Fall through to original combined score

        # sec-eco-06: downgrade higher-classification entries to avoid leakage via ranking
        if classification_penalty:
            level = _classification_level(classification)
            penalty = max(0.1, 1.0 - (level * 0.15))
            combined *= penalty

        results.append((combined, id_, content, type_, importance, created_at))

    results.sort(reverse=True, key=lambda x: x[0])
    return results


# ---------------------------------------------------------------------------
# D-SUM-1: LLM summarization (Phase 72, Hermes adaptation)
# ---------------------------------------------------------------------------
_summary_cache = {}  # query_hash → (summary, timestamp)
_SUMMARY_CACHE_TTL = 3600  # 1 hour


def _summarize_results(query, top_entries, cache_ttl=_SUMMARY_CACHE_TTL):
    """Summarize ranked memory entries into a narrative using scanner-tier LLM.

    Args:
        query: Original search query.
        top_entries: List of (score, content, type_) tuples.
        cache_ttl: Cache TTL in seconds.

    Returns:
        Tuple of (summary_text or None, error_message or None).
    """
    import hashlib
    import time

    # Build cache key from query + entry contents
    entry_key = hashlib.sha256((query + "|" + "|".join(c for _, c, _ in top_entries)).encode()).hexdigest()[:16]

    # Check cache
    if entry_key in _summary_cache:
        cached_summary, cached_ts = _summary_cache[entry_key]
        if time.time() - cached_ts < cache_ttl:
            return cached_summary, None

    # Build entries text for prompt
    entries_text = "\n".join(
        f"- [{t}] (score: {s:.3f}) {c[:200]}"
        for s, c, t in top_entries[:10]  # Cap at 10 for token budget
    )

    # Load hardprompt template
    hardprompt_path = Path(__file__).resolve().parent.parent.parent / "hardprompts" / "memory" / "search_summary.md"
    if hardprompt_path.exists():
        template = hardprompt_path.read_text(encoding="utf-8")
        prompt = template.replace("{query}", query).replace("{entries_text}", entries_text)
    else:
        prompt = (
            f"Synthesize these memory entries into a concise narrative summary.\n"
            f"Query: {query}\n\nEntries:\n{entries_text}"
        )

    try:
        from tools.llm.router import LLMRouter

        router = LLMRouter()

        # Use memory_consolidation function (scanner tier — qwen3.5, zero Claude)
        from tools.llm.provider import LLMRequest

        request = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=512,
        )
        response = router.invoke("memory_consolidation", request)

        # Extract text from response
        summary = response.content.strip() if response and response.content else ""

        if summary:
            _summary_cache[entry_key] = (summary, time.time())
            return summary, None
        return None, "LLM returned empty response"

    except ImportError:
        return None, "LLM router not available"
    except Exception as exc:
        return None, str(exc)


def search(
    query: str,
    limit: int = 5,
    tier: str | None = None,
    user_id: str | None = None,
    tenant_id: str | None = None,
    bm25_weight: float = 0.7,
    semantic_weight: float = 0.3,
) -> list[dict]:
    """Programmatic hybrid search for use by agent_loop and chat_manager.

    Returns a list of dicts: {id, content, type, score}.  Returns [] on any
    error so callers can degrade gracefully.  Tier filtering is applied at
    the Python level and is a no-op if migration 226 has not yet run.

    Args:
        query:    Natural-language search query.
        limit:    Maximum number of results to return (default 5).
        tier:     Optional pipe-separated tier filter, e.g. ``'episodic|semantic'``.
                  Values: ``'procedural'``, ``'episodic'``, ``'semantic'``.
        user_id:  Scope results to this user (or global entries).
        tenant_id: Scope results to this tenant (or global entries).
    """
    if not query or not query.strip():
        return []
    try:
        entries = get_all_entries(user_id=user_id, tenant_id=tenant_id)
        if not entries:
            return []

        # Tier filter — applied after fetch; column may not exist yet (migration 226)
        if tier:
            allowed = {t.strip() for t in tier.split("|") if t.strip()}
            if allowed:
                conn = _connect()
                try:
                    # Try to fetch tier column; fall back silently if not present
                    c = conn.cursor()
                    c.execute("SELECT id, tier FROM memory_entries")
                    tier_map = {row[0]: (row[1] or "episodic") for row in c.fetchall()}
                except Exception:
                    tier_map = {}
                finally:
                    conn.close()
                if tier_map:
                    entries = [e for e in entries if tier_map.get(e[0], "episodic") in allowed]

        if not entries:
            return []

        bm25_scores = bm25_search(query, entries)
        semantic_scores = semantic_search(query, entries)
        ranked = hybrid_rank(entries, bm25_scores, semantic_scores, bm25_weight, semantic_weight)

        return [
            {"id": id_, "content": content, "type": type_, "score": round(score, 4)}
            for score, id_, content, type_, importance, created_at in ranked[:limit]
            if score > 0
        ]
    except Exception:
        return []


def main():
    parser = argparse.ArgumentParser(description="Hybrid search (BM25 + semantic)")
    parser.add_argument("--query", required=True, help="Search query")
    parser.add_argument("--limit", type=int, default=10, help="Max results")
    parser.add_argument("--bm25-weight", type=float, default=0.7, help="BM25 weight (default 0.7)")
    parser.add_argument("--semantic-weight", type=float, default=0.3, help="Semantic weight (default 0.3)")
    parser.add_argument("--time-decay", action="store_true", help="Enable time-decay scoring (D147)")
    parser.add_argument("--user-id", help="Filter by user ID (D180)")
    parser.add_argument("--tenant-id", help="Filter by tenant ID (D180)")
    parser.add_argument(
        "--clearance",
        default=None,
        help="User security clearance for classification filtering",
    )
    parser.add_argument(
        "--compartments",
        default=None,
        help="Comma-separated list of user compartments",
    )
    parser.add_argument(
        "--summarize", action="store_true", help="Synthesize top results into narrative summary via scanner-tier LLM"
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    comps = None
    if args.compartments:
        comps = [c.strip() for c in args.compartments.split(",") if c.strip()]

    entries = get_all_entries(
        user_id=args.user_id,
        tenant_id=args.tenant_id,
        clearance=args.clearance,
        compartments=comps,
    )
    if not entries:
        if args.json:
            print(json.dumps({"classification": "CUI // SP-CTI", "count": 0, "entries": []}))
        else:
            print("No memory entries found.")
        return

    bm25_scores = bm25_search(args.query, entries)
    semantic_scores = semantic_search(args.query, entries)

    if semantic_scores is None and not args.json:
        print("(Semantic search unavailable — using keyword search only)")

    # D147: Load time-decay config if enabled
    decay_config = None
    if args.time_decay:
        try:
            from tools.memory.time_decay import load_decay_config

            decay_config = load_decay_config()
        except (ImportError, Exception):
            if not args.json:
                print("(Time-decay module unavailable — using standard ranking)")

    results = hybrid_rank(
        entries,
        bm25_scores,
        semantic_scores,
        args.bm25_weight,
        args.semantic_weight,
        time_decay_enabled=args.time_decay,
        decay_config=decay_config,
        classification_penalty=args.clearance is not None,
    )

    # Log access
    search_type = "hybrid_time_decay" if args.time_decay else "hybrid"
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "INSERT INTO memory_access_log (query, results_count, search_type) VALUES (%s, %s, %s)",
        (args.query, min(args.limit, len(results)), search_type),
    )
    conn.commit()
    conn.close()

    # D-SUM-1: LLM summarization of search results (Phase 72, Hermes adaptation)
    summary = None
    summary_error = None
    if args.summarize:
        top_entries = [
            (score, content, type_)
            for score, id_, content, type_, importance, created_at in results[: args.limit]
            if score > 0
        ]
        if top_entries:
            summary, summary_error = _summarize_results(args.query, top_entries)

    if args.json:
        output_entries = []
        for score, id_, content, type_, importance, created_at in results[: args.limit]:
            if score > 0:
                output_entries.append(
                    {
                        "id": id_,
                        "score": round(score, 4),
                        "content": content,
                        "type": type_,
                        "importance": importance,
                        "created_at": created_at,
                    }
                )
        output = {
            "classification": "CUI // SP-CTI",
            "count": len(output_entries),
            "search_type": search_type,
            "semantic_available": semantic_scores is not None,
            "entries": output_entries,
        }
        if args.summarize:
            if summary:
                output["summary"] = summary
            if summary_error:
                output["summary_error"] = summary_error
        print(json.dumps(output, indent=2))
    else:
        for score, id_, content, type_, importance, created_at in results[: args.limit]:
            if score > 0:
                print(f"[#{id_}] (score:{score:.3f}, {type_}, importance:{importance}) {content}  — {created_at}")


if __name__ == "__main__":
    main()
