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


# ---------------------------------------------------------------------------
# xrv-mem-03: progressive disclosure -- index -> timeline -> detail by id
# ---------------------------------------------------------------------------
# WHY. The default output (the `full` layer, unchanged) returns the ranked rows
# with their whole content, so every recall paid full-row cost before a reader
# could decide which rows it wanted. claude-mem's measured saving comes from
# filtering BEFORE fetching: a ~40-token index line per hit, expansion by id
# only for the rows that matter. Three layers, each reporting its own cost:
#   index     {id, ts, type, headline<=120, score} for the top-K of a query
#   timeline  index rows inside a time window, chronological, INTERLEAVED with
#             the activity feed's session events (audit_trail UNION ALL
#             hook_events -- the dashboard's own query builder, imported from
#             tools/dashboard/api/activity_query; never a second copy)
#   detail    the full rows for the ids asked for, and ONLY those. Unknown ids
#             are NAMED under `missing_ids`; rows a clearance withholds are
#             NAMED under `withheld_ids`. Neither is an empty list wearing the
#             other's meaning.
# `approx_tokens` on every layered response is context_budget.estimate_tokens
# over the serialised payload (the platform's ONE estimator -- there is no
# third beside it and context_pressure's, which measures a SESSION from
# hook_events and is the wrong instrument for a payload). It is computed over
# the payload WITHOUT the count field itself, so it is the cost of what a
# reader actually receives.
# The headline is session_context.headline -- the same 120-char rule the
# SessionStart index (xrv-mem-01) prints, so an id read off a session start
# block and an id read off `--layer index` describe one entry the same way.
# The `full` layer is byte-identical to the pre-card output and carries NO
# approx_tokens: the default shape is what profile_memory, chat_manager and
# memory_consolidation read today.

LAYERS = ("full", "index", "timeline", "detail")
DEFAULT_LAYER = "full"
DEFAULT_TIMELINE_WINDOW_HOURS = 24
TIMELINE_KINDS = ("memory", "audit", "hook")


def _ts(value) -> str | None:
    """One ISO-8601 spelling for a stored timestamp, for sorting and windows.

    PostgreSQL returns datetimes and SQLite returns whatever was written --
    ``datetime('now')`` spells ``YYYY-MM-DD HH:MM:SS`` while Python's
    ``isoformat()`` spells the ``T`` form, and the two do not compare
    lexicographically (a space sorts before a ``T``), so both are normalised
    to the ``T`` form before any comparison.
    """
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:  # noqa: BLE001 -- a datetime-alike that cannot format
            pass
    text = str(value).strip()
    if len(text) > 10 and text[10] == " ":
        text = text[:10] + "T" + text[11:]
    return text


def approx_tokens(payload) -> int:
    """context_budget.estimate_tokens over the serialised payload."""
    from tools.llm.context_budget import estimate_tokens

    return estimate_tokens(json.dumps(payload, default=str, sort_keys=True))


def _with_cost(payload: dict) -> dict:
    payload = dict(payload)
    payload.pop("approx_tokens", None)
    payload["approx_tokens"] = approx_tokens(payload)
    return payload


def _headline(content) -> str:
    from tools.hooks.session_context import headline

    return headline(content)


def _index_row(score, id_, content, type_, created_at) -> dict:
    return {
        "id": id_,
        "ts": _ts(created_at),
        "type": type_,
        "headline": _headline(content),
        "score": None if score is None else round(score, 4),
    }


def _ranked(query, entries, bm25_weight, semantic_weight, time_decay=False):
    """Rank ``entries`` for ``query`` the way the full layer does."""
    bm25_scores = bm25_search(query, entries)
    semantic_scores = semantic_search(query, entries)
    decay_config = None
    if time_decay:
        try:
            from tools.memory.time_decay import load_decay_config

            decay_config = load_decay_config()
        except Exception:  # noqa: BLE001 -- decay is optional, exactly as in main()
            decay_config = None
    ranked = hybrid_rank(
        entries, bm25_scores, semantic_scores, bm25_weight, semantic_weight,
        time_decay_enabled=time_decay, decay_config=decay_config,
    )
    return ranked, semantic_scores is not None


def _log_access(query, count, search_type) -> bool:
    """Best-effort memory_access_log row through the test seam; never raises."""
    try:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO memory_access_log (query, results_count, search_type) VALUES (%s, %s, %s)",
                (query or "", count, search_type),
            )
            conn.commit()
        finally:
            conn.close()
        return True
    except Exception:  # noqa: BLE001 -- the log is a record of the search, not the search
        return False


def layer_index(
    query: str,
    limit: int = 10,
    *,
    user_id=None,
    tenant_id=None,
    clearance=None,
    compartments=None,
    bm25_weight: float = 0.7,
    semantic_weight: float = 0.3,
    time_decay: bool = False,
) -> dict:
    """Top-K index rows for ``query``: id, ts, type, headline, score."""
    if not query or not query.strip():
        raise ValueError("--query is required for --layer index")
    entries = get_all_entries(user_id=user_id, tenant_id=tenant_id, clearance=clearance, compartments=compartments)
    rows: list[dict] = []
    semantic_available = False
    if entries:
        ranked, semantic_available = _ranked(query, entries, bm25_weight, semantic_weight, time_decay)
        for score, id_, content, type_, _importance, created_at in ranked[:limit]:
            if score > 0:
                rows.append(_index_row(score, id_, content, type_, created_at))
    logged = _log_access(query, len(rows), "hybrid_index")
    return _with_cost({
        "classification": "CUI // SP-CTI",
        "layer": "index",
        "query": query,
        "count": len(rows),
        "limit": limit,
        "search_type": "hybrid_index",
        "semantic_available": semantic_available,
        "access_logged": logged,
        "entries": rows,
    })


def parse_ids(ids) -> tuple[list[int], list[str]]:
    """Split a caller's ids into integer ids and the tokens that are not ids.

    ``memory_entries.id`` is an INTEGER; a non-numeric token is reported under
    ``missing_ids`` without ever reaching SQL (an ``integer = text`` compare
    raises on PostgreSQL and silently matches nothing on SQLite).
    """
    if ids is None:
        tokens: list = []
    elif isinstance(ids, str):
        tokens = [t.strip() for t in ids.split(",")]
    else:
        tokens = [str(t).strip() for t in ids]
    numeric: list[int] = []
    bad: list[str] = []
    seen: set[int] = set()
    for tok in tokens:
        if not tok:
            continue
        text = tok[1:] if tok.startswith("#") else tok
        try:
            value = int(text)
        except ValueError:
            bad.append(tok)
            continue
        if value not in seen:
            seen.add(value)
            numeric.append(value)
    return numeric, bad


def layer_detail(
    ids,
    *,
    user_id=None,
    tenant_id=None,
    clearance=None,
    compartments=None,
) -> dict:
    """Full rows for exactly the given ids, in the order asked."""
    wanted, bad = parse_ids(ids)
    if not wanted and not bad:
        raise ValueError("--ids is required for --layer detail")
    found: dict[int, dict] = {}
    withheld: list[int] = []
    if wanted:
        conn = _connect()
        try:
            ph = "%s"
            # The IN list is placeholders only; every id is bound as an int.
            sql = (
                "SELECT id, content, type, importance, created_at, classification, compartment "  # nosec B608
                "FROM memory_entries WHERE id IN (" + ",".join([ph] * len(wanted)) + ")"
            )
            params: list = list(wanted)
            if user_id:
                sql += f" AND (user_id = {ph} OR user_id IS NULL)"
                params.append(user_id)
            if tenant_id:
                sql += f" AND (tenant_id = {ph} OR tenant_id IS NULL)"
                params.append(tenant_id)
            c = conn.cursor()
            c.execute(sql, params)
            fetched = c.fetchall()
        finally:
            conn.close()
        user_level = _classification_level(clearance) if clearance is not None else None
        for row in fetched:
            id_, content, type_, importance, created_at, classification, compartment = tuple(row)[:7]
            if user_level is not None and not (
                _classification_level(classification) <= user_level
                and _compartments_allowed(compartment, compartments)
            ):
                withheld.append(int(id_))
                continue
            found[int(id_)] = {
                "id": id_,
                "content": content,
                "type": type_,
                "importance": importance,
                "created_at": _ts(created_at),
                "classification": classification,
                "compartment": compartment,
            }
    entries = [found[i] for i in wanted if i in found]
    missing = [str(i) for i in wanted if i not in found and i not in withheld] + bad
    return _with_cost({
        "classification": "CUI // SP-CTI",
        "layer": "detail",
        "requested_ids": [str(i) for i in wanted] + bad,
        "count": len(entries),
        "entries": entries,
        "missing_ids": missing,
        "withheld_ids": [str(i) for i in withheld],
    })


def _activity_events(since, until, limit, session_id) -> tuple[list[dict], dict]:
    """The activity feed's rows inside the window, newest-K, through the ONE builder.

    Returns ``(events, status)`` where status is ``{"status": "ok"}`` or
    ``{"status": "unmeasurable", "reason": "error:<Type>"}`` -- a database with
    no audit_trail/hook_events (a fixture, a child app) is not a session with
    no events.
    """
    from tools.dashboard.api.activity_query import build_feed_query

    try:
        conn = _connect()
        try:
            from tools.db.storage import is_pg, sql_placeholder

            ph = sql_placeholder(conn)
            sql, params = build_feed_query(
                ph, since=since, until=until, actor=session_id, order="DESC", limit=limit,
                normalise_ts=not is_pg(conn),
            )
            c = conn.cursor()
            c.execute(sql, params)
            rows = c.fetchall()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 -- reported as unmeasurable, never as empty
        return [], {"status": "unmeasurable", "reason": f"error:{type(exc).__name__}"}
    events = []
    for row in rows:
        source, id_, event_type, actor, summary, project_id, _classification, created_at = tuple(row)[:8]
        events.append({
            "kind": source,
            "id": id_,
            "ts": _ts(created_at),
            "type": event_type,
            "headline": _headline(summary if summary else actor),
            "actor": actor,
            "project_id": project_id,
            "score": None,
        })
    return events, {"status": "ok"}


def layer_timeline(
    query: str | None = None,
    *,
    since: str | None = None,
    until: str | None = None,
    limit: int = 10,
    session_id: str | None = None,
    user_id=None,
    tenant_id=None,
    clearance=None,
    compartments=None,
    bm25_weight: float = 0.7,
    semantic_weight: float = 0.3,
    time_decay: bool = False,
) -> dict:
    """Index rows in a window, chronological, interleaved with session events.

    With a query the memory rows are the top-K by hybrid score inside the
    window (then re-sorted by time); without one they are the newest K.
    ``limit`` bounds EACH side and both counts are reported.
    """
    from datetime import datetime, timedelta, timezone

    window_basis = "explicit"
    if not since and not until:
        since = (datetime.now(timezone.utc) - timedelta(hours=DEFAULT_TIMELINE_WINDOW_HOURS)).isoformat()
        window_basis = f"default_{DEFAULT_TIMELINE_WINDOW_HOURS}h"
    since_n = _ts(since)
    until_n = _ts(until)

    entries = get_all_entries(user_id=user_id, tenant_id=tenant_id, clearance=clearance, compartments=compartments)
    in_window = []
    for entry in entries:
        ts = _ts(entry[5])
        if ts is None:
            continue
        if since_n and ts < since_n:
            continue
        if until_n and ts > until_n:
            continue
        in_window.append(entry)

    memory_rows: list[dict] = []
    semantic_available = None
    if in_window:
        if query and query.strip():
            ranked, semantic_available = _ranked(query, in_window, bm25_weight, semantic_weight, time_decay)
            for score, id_, content, type_, _importance, created_at in ranked[:limit]:
                if score > 0:
                    memory_rows.append(_index_row(score, id_, content, type_, created_at))
        else:
            newest = sorted(in_window, key=lambda e: _ts(e[5]) or "", reverse=True)[:limit]
            for entry in newest:
                id_, content, type_, _importance, _emb, created_at = tuple(entry)[:6]
                memory_rows.append(_index_row(None, id_, content, type_, created_at))
    for row in memory_rows:
        row["kind"] = "memory"

    activity, activity_status = _activity_events(since_n, until_n, limit, session_id)
    events = sorted(memory_rows + activity, key=lambda e: (e["ts"] or "", str(e["id"])))
    logged = _log_access(query, len(memory_rows), "hybrid_timeline")
    return _with_cost({
        "classification": "CUI // SP-CTI",
        "layer": "timeline",
        "query": query or None,
        "window": {"since": since_n, "until": until_n, "basis": window_basis},
        "limit": limit,
        "session_id": session_id,
        "counts": {"memory": len(memory_rows), "activity": len(activity), "total": len(events)},
        "semantic_available": semantic_available,
        "activity": activity_status,
        "access_logged": logged,
        "events": events,
    })


def run_layer(layer: str, **kwargs) -> dict:
    """Dispatch one non-default layer; the MCP handler's entry point.

    Accepted kwargs: query, ids, since, until, limit, session_id, user_id,
    tenant_id, clearance, compartments, bm25_weight, semantic_weight,
    time_decay. ``full`` is deliberately not dispatched here -- its output is
    main()'s and the MCP handler's default is a different search altogether.
    """
    if layer not in LAYERS or layer == "full":
        raise ValueError(f"layer must be one of {LAYERS[1:]}, got {layer!r}")
    scope = {k: kwargs.get(k) for k in ("user_id", "tenant_id", "clearance", "compartments")}
    if layer == "detail":
        return layer_detail(kwargs.get("ids"), **scope)
    weights = {
        "bm25_weight": kwargs.get("bm25_weight", 0.7),
        "semantic_weight": kwargs.get("semantic_weight", 0.3),
        "time_decay": bool(kwargs.get("time_decay", False)),
    }
    limit = int(kwargs.get("limit") or 10)
    if layer == "index":
        return layer_index(kwargs.get("query"), limit, **scope, **weights)
    return layer_timeline(
        kwargs.get("query"),
        since=kwargs.get("since"),
        until=kwargs.get("until"),
        limit=limit,
        session_id=kwargs.get("session_id"),
        **scope,
        **weights,
    )


def _print_layer(result: dict) -> None:
    layer = result["layer"]
    if layer == "detail":
        for e in result["entries"]:
            print(f"[#{e['id']}] ({e['type']}, importance:{e['importance']}) {e['content']}  -- {e['created_at']}")
        if result["missing_ids"]:
            print(f"missing: {', '.join(result['missing_ids'])}")
        if result["withheld_ids"]:
            print(f"withheld by clearance: {', '.join(result['withheld_ids'])}")
    elif layer == "index":
        for e in result["entries"]:
            print(f"#{e['id']} [{e['type']}] {e['ts']} (score:{e['score']:.3f}) {e['headline']}")
    else:
        w = result["window"]
        print(f"window {w['since']} .. {w['until'] or 'now'} ({w['basis']}); activity: {result['activity']['status']}")
        for e in result["events"]:
            tag = f"#{e['id']}" if e["kind"] == "memory" else f"{e['kind']}:{e['id']}"
            score = "" if e["score"] is None else f" (score:{e['score']:.3f})"
            print(f"{e['ts']} {tag} [{e['type']}]{score} {e['headline']}")
    print(f"approx_tokens: {result['approx_tokens']}")


def _run_layer(args) -> None:
    comps = None
    if args.compartments:
        comps = [c.strip() for c in args.compartments.split(",") if c.strip()]
    result = run_layer(
        args.layer,
        query=args.query,
        ids=args.ids,
        since=args.since,
        until=args.until,
        limit=args.limit,
        session_id=args.session_id,
        user_id=args.user_id,
        tenant_id=args.tenant_id,
        clearance=args.clearance,
        compartments=comps,
        bm25_weight=args.bm25_weight,
        semantic_weight=args.semantic_weight,
        time_decay=args.time_decay,
    )
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        _print_layer(result)


def main():
    parser = argparse.ArgumentParser(description="Hybrid search (BM25 + semantic)")
    parser.add_argument("--query", help="Search query (required for --layer full|index)")
    # xrv-mem-03: progressive disclosure. The default layer is byte-identical
    # to the pre-card output; the three others carry approx_tokens.
    parser.add_argument("--layer", choices=LAYERS, default=DEFAULT_LAYER,
                        help="full (default, the ranked rows with content) | index | timeline | detail")
    parser.add_argument("--ids", help="Comma-separated memory_entries ids for --layer detail")
    parser.add_argument("--since", help="ISO-8601 lower bound for --layer timeline (default: last 24h)")
    parser.add_argument("--until", help="ISO-8601 upper bound for --layer timeline")
    parser.add_argument("--session-id", help="Narrow --layer timeline's session events to one session id")
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

    if args.layer in ("full", "index") and not args.query:
        parser.error("the following arguments are required: --query")
    if args.layer == "detail" and not args.ids:
        parser.error("--layer detail requires --ids")
    if args.layer != DEFAULT_LAYER:
        try:
            _run_layer(args)
        except ValueError as exc:
            parser.error(str(exc))
        return

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
