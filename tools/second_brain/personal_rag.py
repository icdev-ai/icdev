# CUI // SP-CTI
"""Personal RAG — ingest URLs/text into the user's scoped knowledge base."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)


def _conn():
    from tools.second_brain.constants import BRIEFING_ENV_FLAG
    from tools.db.storage import get_canvas_connection
    return get_canvas_connection(BRIEFING_ENV_FLAG)


def queue_url(user_id: str, url: str, title: str = "", tenant_id: str = "default") -> dict:
    """Queue a URL for background ingestion."""
    from tools.db.storage import sql_placeholder
    item_id = str(uuid.uuid4())
    with _conn() as conn:
        ph = sql_placeholder(conn)
        conn.execute(
            f"INSERT INTO user_knowledge_items "
            f"(id,user_id,tenant_id,source_type,source_url,title,status) "
            f"VALUES ({ph},{ph},{ph},'url',{ph},{ph},'pending')",
            (item_id, user_id, tenant_id, url, title or url[:80]),
        )
        conn.commit()
    _trigger_ingest(item_id, user_id, url, tenant_id)
    return {"id": item_id, "status": "pending"}


def queue_text(
    user_id: str,
    text: str,
    title: str = "",
    tags: list[str] | None = None,
    tenant_id: str = "default",
) -> dict:
    """Store free text directly (no fetch needed)."""
    from tools.db.storage import sql_placeholder
    item_id = str(uuid.uuid4())
    with _conn() as conn:
        ph = sql_placeholder(conn)
        conn.execute(
            f"INSERT INTO user_knowledge_items "
            f"(id,user_id,tenant_id,source_type,title,raw_content,tags,status) "
            f"VALUES ({ph},{ph},{ph},'text',{ph},{ph},{ph},'done')",
            (item_id, user_id, tenant_id, title or "Note", text, json.dumps(tags or [])),
        )
        conn.commit()
    _index_item(item_id, user_id, text, title, tenant_id)
    return {"id": item_id, "status": "done"}


def get_items(user_id: str, tenant_id: str = "default", limit: int = 20) -> list[dict]:
    from tools.db.storage import sql_placeholder
    try:
        with _conn() as conn:
            ph = sql_placeholder(conn)
            rows = conn.execute(
                f"SELECT id,source_type,source_url,title,summary,tags,status,created_at "
                f"FROM user_knowledge_items WHERE user_id={ph} AND tenant_id={ph} "
                f"ORDER BY created_at DESC LIMIT {limit}",
                (user_id, tenant_id),
            ).fetchall()
        cols = ["id", "source_type", "url", "title", "summary", "tags", "status", "created_at"]
        return [dict(zip(cols, r)) for r in rows]
    except Exception:
        return []


def delete_item(item_id: str, user_id: str, tenant_id: str = "default") -> bool:
    from tools.db.storage import sql_placeholder
    try:
        with _conn() as conn:
            ph = sql_placeholder(conn)
            conn.execute(
                f"DELETE FROM user_knowledge_items "
                f"WHERE id={ph} AND user_id={ph} AND tenant_id={ph}",
                (item_id, user_id, tenant_id),
            )
            conn.commit()
        return True
    except Exception:
        return False


def search_personal_rag(
    query: str,
    user_id: str,
    tenant_id: str = "default",
    limit: int = 5,
) -> list[dict]:
    """Simple keyword search over personal knowledge items."""
    from tools.db.storage import sql_placeholder
    try:
        with _conn() as conn:
            ph = sql_placeholder(conn)
            rows = conn.execute(
                f"SELECT id,title,summary,source_url,created_at FROM user_knowledge_items "
                f"WHERE user_id={ph} AND tenant_id={ph} AND status='done' "
                f"AND (title LIKE {ph} OR summary LIKE {ph} OR raw_content LIKE {ph}) "
                f"ORDER BY created_at DESC LIMIT {limit}",
                (user_id, tenant_id, f"%{query}%", f"%{query}%", f"%{query}%"),
            ).fetchall()
        cols = ["id", "title", "summary", "url", "date"]
        return [dict(zip(cols, r)) for r in rows]
    except Exception:
        return []


def _trigger_ingest(item_id: str, user_id: str, url: str, tenant_id: str) -> None:
    """Background-fetch URL content and store in knowledge item."""
    import threading

    def _work() -> None:
        try:
            import re as _re
            import urllib.request
            with urllib.request.urlopen(url, timeout=10) as resp:
                raw = resp.read(50_000).decode("utf-8", errors="ignore")
            clean = _re.sub(r"<[^>]+>", " ", raw)
            clean = _re.sub(r"\s+", " ", clean).strip()[:8000]
            _index_item(item_id, user_id, clean, url, tenant_id)
        except Exception as exc:
            logger.debug("[personal_rag] fetch failed for %s: %s", url, exc)
            _mark_error(item_id, str(exc))

    threading.Thread(target=_work, daemon=True).start()


def _index_item(item_id: str, user_id: str, content: str, title: str, tenant_id: str) -> None:
    """Summarise content and persist; write to memory_entries for RAG retrieval."""
    from tools.db.storage import sql_placeholder
    summary = content[:300].replace("\n", " ")
    now = datetime.now(timezone.utc).isoformat()
    try:
        with _conn() as conn:
            ph = sql_placeholder(conn)
            conn.execute(
                f"UPDATE user_knowledge_items "
                f"SET summary={ph},raw_content={ph},status='done',indexed_at={ph} WHERE id={ph}",
                (summary, content[:8000], now, item_id),
            )
            conn.commit()
    except Exception as exc:
        logger.debug("[personal_rag] index update failed: %s", exc)
    try:
        from tools.memory.memory_write import write_memory
        write_memory(
            content=f"[Personal KB] {title}: {summary}",
            memory_type="semantic",
            user_id=user_id,
            tags=["personal_rag", f"user:{user_id}"],
        )
    except Exception:
        pass


def _mark_error(item_id: str, msg: str) -> None:
    from tools.db.storage import sql_placeholder
    try:
        with _conn() as conn:
            ph = sql_placeholder(conn)
            conn.execute(
                f"UPDATE user_knowledge_items SET status='error',error_msg={ph} WHERE id={ph}",
                (msg[:200], item_id),
            )
            conn.commit()
    except Exception:
        pass
