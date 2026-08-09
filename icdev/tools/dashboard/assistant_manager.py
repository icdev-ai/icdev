#!/usr/bin/env python3
# CUI // SP-CTI
"""Codebase Assistant backend manager (Phase 69 — D-CA-5 through D-CA-8).

Handles queries from the floating chat widget: question answering with RAG
retrieval, caching, contextual suggestions, and status reporting.

Usage:
    from tools.dashboard.assistant_manager import query, get_suggestions, get_status
"""

from __future__ import annotations
import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.logging.icdev_logger import get_logger

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = get_logger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# ---------------------------------------------------------------------------
# Cache threshold — after this many identical question hashes, cache the answer
# ---------------------------------------------------------------------------
_CACHE_PROMOTION_THRESHOLD = 3

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


def _get_conn():
    """Return a DB connection via the shared storage layer."""
    from tools.db.storage import get_connection

    return get_connection()


def _ensure_tables(conn) -> None:
    """Create assistant-specific tables if they don't already exist.

    chat_contexts and chat_messages are created by init_icdev_db.py (Phase 44).
    codebase_qa_cache and codebase_index are Phase 69 additions.
    """
    from tools.db.storage import translate_sql
    stmts = [
        translate_sql("""CREATE TABLE IF NOT EXISTS codebase_qa_cache (
            id TEXT PRIMARY KEY,
            question_hash TEXT NOT NULL,
            scope TEXT DEFAULT '',
            question_text TEXT NOT NULL,
            answer_text TEXT NOT NULL,
            source_citations TEXT DEFAULT '[]',
            hit_count INTEGER DEFAULT 1,
            last_hit_at TEXT DEFAULT (datetime('now')),
            created_at TEXT DEFAULT (datetime('now'))
        )"""),
        translate_sql(
            "CREATE INDEX IF NOT EXISTS idx_qa_cache_hash "
            "ON codebase_qa_cache(question_hash, scope)"
        ),
        translate_sql("""CREATE TABLE IF NOT EXISTS codebase_index (
            id TEXT PRIMARY KEY,
            file_path TEXT NOT NULL,
            file_hash TEXT NOT NULL DEFAULT '',
            file_type TEXT NOT NULL DEFAULT 'python',
            module TEXT DEFAULT '',
            symbols TEXT DEFAULT '[]',
            last_indexed_at TEXT,
            chunk_count INTEGER DEFAULT 0,
            classification TEXT DEFAULT 'CUI'
        )"""),
        translate_sql(
            "CREATE INDEX IF NOT EXISTS idx_codebase_idx_module "
            "ON codebase_index(module)"
        ),
    ]
    for stmt in stmts:
        try:
            conn.execute(stmt)
        except Exception:
            pass  # index/table already exists
    conn.commit()


# ---------------------------------------------------------------------------
# Context management
# ---------------------------------------------------------------------------


def get_or_create_context(context_id: str | None = None) -> str:
    """Return a valid chat context ID, creating one if necessary.

    Args:
        context_id: Existing context ID to verify.  If *None* or not found
                    in ``chat_contexts``, a fresh context is created with
                    ``source='widget'``.

    Returns:
        The verified (or newly created) context ID string.
    """
    conn = _get_conn()
    _ensure_tables(conn)
    cur = conn.cursor()

    if context_id:
        cur.execute("SELECT id FROM chat_contexts WHERE id = %s", (context_id,))
        row = cur.fetchone()
        if row:
            return str(row[0])

    # Create new context
    new_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    cur.execute(
        """INSERT INTO chat_contexts
               (id, user_id, title, status, classification, created_at, updated_at)
           VALUES (%s, 'widget', 'Codebase Assistant', 'active', 'CUI', %s, %s)""",
        (new_id, now, now),
    )
    conn.commit()
    return new_id


# ---------------------------------------------------------------------------
# RAG retrieval
# ---------------------------------------------------------------------------


def _rag_retrieve(query_text: str, scope: str | None = None, top_k: int = 5) -> list[dict[str, Any]]:
    """Retrieve relevant code chunks for the question.

    Attempts the full RAG retriever first; falls back to simple FTS on
    ``codebase_index`` when the RAG subsystem is unavailable.
    """
    # --- Primary path: RAG retriever ---
    try:
        from tools.rag.retriever import RAGRetriever

        retriever = RAGRetriever()
        source_types = ["codebase"] if not scope else []
        results = retriever.search(query_text, top_k=top_k, source_types=source_types)
        chunks = []
        for r in results:
            meta = r.metadata if hasattr(r, "metadata") else {}
            chunks.append(
                {
                    "content": r.content,
                    "file": meta.get("file_path", ""),
                    "line": meta.get("line", 0),
                    "score": round(getattr(r, "final_score", 0.0), 4),
                }
            )
        if chunks:
            return chunks
    except (ImportError, Exception) as exc:  # noqa: BLE001
        logger.debug("RAG retriever unavailable, falling back to FTS: %s", exc)

    # --- Fallback: SQLite FTS on codebase_index ---
    try:
        conn = _get_conn()
        _ensure_tables(conn)
        cur = conn.cursor()
        params: list[Any] = []
        sql = "SELECT file_path, symbols FROM codebase_index"
        from tools.db.storage import sql_placeholder as _sqlph
        ph = _sqlph(conn)
        if scope:
            sql += f" WHERE module = {ph}"
            params.append(scope)
        sql += f" LIMIT {ph}"
        params.append(top_k * 3)  # over-fetch for simple relevance filter
        cur.execute(sql, params)

        rows = cur.fetchall()
        # Simple keyword overlap ranking
        keywords = set(query_text.lower().split())
        scored: list[tuple[float, dict]] = []
        for row in rows:
            file_path = row[0]
            symbols_json = row[1] if len(row) > 1 else ""
            text = f"{file_path} {symbols_json}".lower()
            overlap = sum(1 for kw in keywords if kw in text)
            if overlap > 0:
                scored.append(
                    (
                        overlap,
                        {
                            "content": symbols_json or "",
                            "file": file_path,
                            "line": 0,
                            "score": overlap / max(len(keywords), 1),
                        },
                    )
                )
        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored[:top_k]]
    except Exception as exc:  # noqa: BLE001
        logger.warning("FTS fallback failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Related pages
# ---------------------------------------------------------------------------


def _find_related_pages(answer: str, scope: str | None = None) -> list[str]:
    """Scan the answer text for mentions of known dashboard modules.

    Returns a list of dashboard page URLs whose module names appear in
    the answer body.
    """
    from tools.dashboard.assistant_config import ROUTE_MODULE_MAP

    answer_lower = answer.lower()
    pages: list[str] = []
    for route, module in ROUTE_MODULE_MAP.items():
        # Check if the module directory name appears in the answer
        module_name = module.rstrip("/").split("/")[-1]
        if module_name and module_name.lower() in answer_lower:
            if route not in pages:
                pages.append(route)
    # Exclude the current scope's route to avoid self-references
    if scope:
        pages = [p for p in pages if ROUTE_MODULE_MAP.get(p) != scope]
    return pages[:5]


# ---------------------------------------------------------------------------
# Citation parsing
# ---------------------------------------------------------------------------

_CITATION_RE = re.compile(
    r"(?:^|\s)(?:(?:file|File|FILE)[:\s]+)?([a-zA-Z0-9_/\\.\-]+\.(?:py|yaml|yml|md|html|json|js|ts))"
    r"(?::(\d+))?",
)


def _parse_citations(answer: str) -> list[dict[str, Any]]:
    """Extract file:line citations from the LLM answer text."""
    citations: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in _CITATION_RE.finditer(answer):
        file_path = match.group(1).replace("\\", "/")
        line = int(match.group(2)) if match.group(2) else 0
        key = f"{file_path}:{line}"
        if key in seen:
            continue
        seen.add(key)

        # Try to read a snippet from the file
        snippet = ""
        full = BASE_DIR / file_path
        if full.is_file() and line > 0:
            try:
                lines = full.read_text(encoding="utf-8").splitlines()
                start = max(0, line - 1)
                end = min(len(lines), line + 2)
                snippet = "\n".join(lines[start:end])
            except Exception:  # noqa: BLE001
                pass

        citations.append({"file": file_path, "line": line, "snippet": snippet})
    return citations


# ---------------------------------------------------------------------------
# Main query handler
# ---------------------------------------------------------------------------


def query(
    question: str,
    scope: str | None = None,
    context_id: str | None = None,
    page_path: str | None = None,
) -> dict[str, Any]:
    """Answer a codebase question using RAG + LLM with caching.

    Args:
        question:   Natural-language question from the user.
        scope:      Module directory scope (e.g. ``'tools/pulse/'``).
        context_id: Optional existing chat context to continue.
        page_path:  Dashboard URL path (e.g. ``'/pulse'``) — used to auto-
                    resolve scope if *scope* is not provided.

    Returns:
        Dict with keys: answer, citations, source, context_id, related_pages.
    """
    # 1. Auto-resolve scope from page path
    if page_path and not scope:
        from tools.dashboard.assistant_config import scope_for_path

        scope = scope_for_path(page_path)

    scope = scope or ""

    # 2. Normalize question and compute hash
    normalized = " ".join(question.strip().lower().split())
    q_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    conn = _get_conn()
    _ensure_tables(conn)
    cur = conn.cursor()

    # 3. Check cache
    cur.execute(
        """SELECT id, answer_text, source_citations, hit_count
           FROM codebase_qa_cache
           WHERE question_hash = %s AND scope = %s
           LIMIT 1""",
        (q_hash, scope),
    )
    cache_row = cur.fetchone()

    if cache_row and cache_row[3] >= _CACHE_PROMOTION_THRESHOLD:
        # Cache hit — increment counters and return
        now = datetime.now(timezone.utc).isoformat()
        cur.execute(
            "UPDATE codebase_qa_cache SET hit_count = hit_count + 1, last_hit_at = %s WHERE id = %s",
            (now, cache_row[0]),
        )
        conn.commit()
        ctx = get_or_create_context(context_id)
        cached_citations = json.loads(cache_row[2]) if cache_row[2] else []
        return {
            "answer": cache_row[1],
            "citations": cached_citations,
            "source": "cache",
            "context_id": ctx,
            "related_pages": _find_related_pages(cache_row[1], scope),
        }

    # 4. RAG retrieval
    rag_chunks = _rag_retrieve(question, scope, top_k=5)

    # 5. Build system prompt
    rag_context = ""
    if rag_chunks:
        parts = []
        for i, chunk in enumerate(rag_chunks, 1):
            ref = chunk.get("file", "unknown")
            if chunk.get("line"):
                ref += f":{chunk['line']}"
            parts.append(f"[{i}] {ref}\n{chunk.get('content', '')}")
        rag_context = "\n\n".join(parts)

    scope_label = scope or "global (all modules)"
    system_prompt = (
        "You are the ICDEV™ Codebase Assistant. Answer questions about the "
        "ICDEV™ codebase accurately and concisely. When referencing code, "
        "cite the file path and line number (e.g. tools/pulse/scheduler.py:42).\n\n"
        f"Current scope: {scope_label}\n"
    )
    if rag_context:
        system_prompt += (
            "\nRelevant code context:\n"
            "---\n"
            f"{rag_context}\n"
            "---\n"
            "Use the context above to answer. Cite file:line when possible."
        )

    # 6. Call LLM
    answer = ""
    try:
        from tools.llm.provider import LLMRequest
        from tools.llm.router import LLMRouter

        router = LLMRouter()
        request = LLMRequest(
            messages=[{"role": "user", "content": question}],
            system_prompt=system_prompt,
            max_tokens=2048,
        )
        response = router.invoke("codebase_query", request)
        answer = response.content.strip() if response and response.content else ""
    except Exception as exc:  # noqa: BLE001
        logger.error("LLM invocation failed: %s", exc)
        answer = (
            "I was unable to generate an answer at this time. "
            "Please check that an LLM provider is configured and available."
        )

    # 7. Parse citations
    citations = _parse_citations(answer)

    # 8. Store conversation
    ctx = get_or_create_context(context_id)
    now = datetime.now(timezone.utc).isoformat()

    # Determine next turn number
    cur.execute(
        "SELECT COALESCE(MAX(turn_number), 0) FROM chat_messages WHERE context_id = %s",
        (ctx,),
    )
    max_turn = cur.fetchone()[0]

    # User message
    meta_json = json.dumps({"scope": scope, "page_path": page_path})
    cur.execute(
        """INSERT INTO chat_messages
               (context_id, turn_number, role, content,
                content_type, metadata, classification, created_at)
           VALUES (%s, %s, 'user', %s, 'text', %s, 'CUI', %s)""",
        (ctx, max_turn + 1, question, meta_json, now),
    )

    # Assistant message
    cur.execute(
        """INSERT INTO chat_messages
               (context_id, turn_number, role, content,
                content_type, metadata, classification, created_at)
           VALUES (%s, %s, 'assistant', %s, 'text', %s, 'CUI', %s)""",
        (
            ctx,
            max_turn + 2,
            answer,
            json.dumps({"citations": citations, "source": "llm"}),
            now,
        ),
    )

    # Update context activity
    cur.execute(
        "UPDATE chat_contexts SET message_count = message_count + 2, last_activity_at = %s, updated_at = %s WHERE id = %s",
        (now, now, ctx),
    )
    conn.commit()

    # 9. Cache promotion — if this hash has been seen enough times
    cur.execute(
        "SELECT hit_count FROM codebase_qa_cache WHERE question_hash = %s AND scope = %s",
        (q_hash, scope),
    )
    existing = cur.fetchone()

    if existing:
        # Update existing cache entry
        cur.execute(
            "UPDATE codebase_qa_cache "
            "SET hit_count = hit_count + 1, "
            "answer_text = %s, source_citations = %s, last_hit_at = %s "
            "WHERE question_hash = %s AND scope = %s",
            (answer, json.dumps(citations), now, q_hash, scope),
        )
    else:
        # Count how many times this hash has appeared in chat_messages
        cur.execute(
            """SELECT COUNT(*) FROM chat_messages
               WHERE role = 'user' AND content = %s""",
            (question,),
        )
        occurrence_count = cur.fetchone()[0]
        if occurrence_count >= _CACHE_PROMOTION_THRESHOLD:
            cache_id = str(uuid.uuid4())
            cit_json = json.dumps(citations)
            cur.execute(
                """INSERT INTO codebase_qa_cache
                       (id, question_hash, scope, question_text,
                        answer_text, source_citations, hit_count,
                        created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    cache_id,
                    q_hash,
                    scope,
                    question,
                    answer,
                    cit_json,
                    occurrence_count,
                    now,
                ),
            )
    conn.commit()

    # 10. Related pages
    related = _find_related_pages(answer, scope)

    return {
        "answer": answer,
        "citations": citations,
        "source": "llm",
        "context_id": ctx,
        "related_pages": related,
    }


# ---------------------------------------------------------------------------
# Suggestions
# ---------------------------------------------------------------------------


def get_suggestions(page_path: str, scope: str | None = None) -> list[str]:
    """Generate 3-5 contextual question suggestions for the current page.

    Reads the ``codebase_index`` table for files in scope and extracts
    top function/class names from the ``symbols`` JSON column.
    """
    if not scope and page_path:
        from tools.dashboard.assistant_config import scope_for_path

        scope = scope_for_path(page_path)

    suggestions: list[str] = []

    if not scope:
        return [
            "What modules make up the ICDEV™ architecture?",
            "How does the FORGE framework work?",
            "What databases does ICDEV™ use?",
        ]

    try:
        conn = _get_conn()
        _ensure_tables(conn)
        cur = conn.cursor()
        cur.execute(
            "SELECT file_path, symbols FROM codebase_index WHERE module = %s LIMIT 20",
            (scope,),
        )
        rows = cur.fetchall()

        functions: list[str] = []
        classes: list[str] = []
        modules: set[str] = set()

        for file_path, symbols_json in rows:
            # Collect module name
            fname = Path(file_path).stem
            if fname != "__init__":
                modules.add(fname)

            # Parse symbols
            try:
                syms = json.loads(symbols_json) if symbols_json else []
            except (json.JSONDecodeError, TypeError):
                syms = []

            for sym in syms:
                name = sym if isinstance(sym, str) else sym.get("name", "")
                kind = "" if isinstance(sym, str) else sym.get("kind", "")
                if kind == "class" or (name and name[0].isupper()):
                    classes.append(name)
                elif name and not name.startswith("_"):
                    functions.append(name)

        # Build suggestions from discovered symbols
        if functions:
            suggestions.append(f"How does {functions[0]}() work?")
        if classes:
            suggestions.append(f"What is the purpose of the {classes[0]} class?")
        if modules:
            mod = sorted(modules)[0]
            suggestions.append(f"What does the {mod} module do?")

        scope_name = scope.rstrip("/").split("/")[-1]
        suggestions.append(f"What are the main entry points for {scope_name}?")
        suggestions.append(f"How is {scope_name} tested?")

    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to generate suggestions: %s", exc)
        scope_name = scope.rstrip("/").split("/")[-1]
        suggestions = [
            f"What does the {scope_name} module do?",
            f"What are the main entry points for {scope_name}?",
            f"How is {scope_name} tested?",
        ]

    return suggestions[:5]


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def get_status() -> dict[str, Any]:
    """Return codebase assistant index status.

    Returns:
        Dict with indexed_files, last_indexed_at, index_status, and
        available_modules.
    """
    try:
        conn = _get_conn()
        _ensure_tables(conn)
        cur = conn.cursor()

        cur.execute("SELECT COUNT(*) FROM codebase_index")
        indexed_files = cur.fetchone()[0]

        cur.execute("SELECT MAX(last_indexed_at) FROM codebase_index")
        last_row = cur.fetchone()
        last_indexed_at = last_row[0] if last_row and last_row[0] else None

        cur.execute("SELECT DISTINCT module FROM codebase_index WHERE module IS NOT NULL AND module != ''")
        available_modules = sorted(row[0] for row in cur.fetchall())

        index_status = "ready" if indexed_files > 0 else "empty"

        return {
            "indexed_files": indexed_files,
            "last_indexed_at": last_indexed_at,
            "index_status": index_status,
            "available_modules": available_modules,
        }
    except Exception as exc:  # noqa: BLE001
        logger.error("Status check failed: %s", exc)
        return {
            "indexed_files": 0,
            "last_indexed_at": None,
            "index_status": "error",
            "available_modules": [],
        }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    status = get_status()
    if "--json" in sys.argv:
        print(json.dumps(status, indent=2))
    else:
        print("Codebase Assistant Status")
        print(f"  Indexed files   : {status['indexed_files']}")
        print(f"  Last indexed at : {status['last_indexed_at'] or 'never'}")
        print(f"  Index status    : {status['index_status']}")
        print(f"  Modules         : {', '.join(status['available_modules']) or 'none'}")
