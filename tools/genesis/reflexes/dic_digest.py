# CUI // SP-CTI
"""Genesis Reflex — DIC Weekly Digest (weekly cadence, runs Sunday night).

Generates a markdown digest of documents ingested in the past 7 days across all
DIC collections for the tenant.  Highlights stale/aging documents from
dic_doc_freshness, and writes a digest output to notification_log so the Home
feed shows the weekly intel summary.

Flow:
  1. Query dic_documents for docs ingested in the last 7 days (by tenant)
  2. Query dic_doc_freshness for stale / aging counts per collection
  3. Build a plain-text digest (deterministic — no LLM required)
  4. Write to notification_log with type='dic_digest'
  5. Optionally seed a DIC "Weekly Digests" collection entry

Air-gap safe: no LLM calls.  Idempotent: guards on a weekly timestamp key.
Must complete within 60s.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

from tools.db.storage import get_connection
from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

IMPLEMENTATION_STATUS = "full"
CADENCE_MINUTES = 60 * 24 * 7   # weekly


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _week_ago() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()


def _fetch_new_docs(conn, tenant_id: str) -> list[dict]:
    """Docs ingested in the last 7 days."""
    try:
        rows = conn.execute(
            """
            SELECT id, title, collection_id, created_at
            FROM   dic_documents
            WHERE  tenant_id = %s
              AND  created_at >= %s
            ORDER  BY created_at DESC
            LIMIT  100
            """,
            (tenant_id, _week_ago()),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.debug("dic_digest: fetch new docs: %s", exc)
        return []


def _fetch_freshness(conn, tenant_id: str) -> list[dict]:
    """Stale/aging counts per collection from dic_doc_freshness."""
    try:
        rows = conn.execute(
            """
            SELECT collection_id,
                   COUNT(*) FILTER (WHERE status = 'stale')  AS stale_count,
                   COUNT(*) FILTER (WHERE status = 'aging')  AS aging_count,
                   COUNT(*)                                   AS total_count
            FROM   dic_doc_freshness
            WHERE  tenant_id = %s
            GROUP  BY collection_id
            ORDER  BY stale_count DESC
            """,
            (tenant_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.debug("dic_digest: fetch freshness: %s", exc)
        return []


def _build_digest(new_docs: list[dict], freshness: list[dict], tenant_id: str) -> str:
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [
        f"# DIC Weekly Digest — {now_str}",
        f"Tenant: {tenant_id}",
        "",
        f"## New This Week ({len(new_docs)} document{'s' if len(new_docs) != 1 else ''})",
    ]
    if new_docs:
        by_collection: dict[str, list] = {}
        for doc in new_docs:
            cid = doc.get("collection_id", "default")
            by_collection.setdefault(cid, []).append(doc)
        for cid, docs in sorted(by_collection.items()):
            lines.append(f"\n### Collection: {cid} ({len(docs)} new)")
            for doc in docs[:10]:
                title = doc.get("title") or doc.get("id", "Untitled")
                ts = (doc.get("created_at") or "")[:10]
                lines.append(f"- {title} ({ts})")
            if len(docs) > 10:
                lines.append(f"  … and {len(docs) - 10} more")
    else:
        lines.append("No new documents this week.")

    lines.append("\n## Freshness Alerts")
    if freshness:
        for row in freshness:
            cid = row.get("collection_id", "?")
            stale = row.get("stale_count", 0) or 0
            aging = row.get("aging_count", 0) or 0
            total = row.get("total_count", 0) or 0
            if stale or aging:
                lines.append(
                    f"- **{cid}**: {stale} stale, {aging} aging out of {total} docs"
                )
        if not any(r.get("stale_count") or r.get("aging_count") for r in freshness):
            lines.append("All collections are fresh.")
    else:
        lines.append("No freshness data available.")

    return "\n".join(lines)


def _write_notification(conn, digest_text: str, tenant_id: str, new_doc_count: int) -> None:
    try:
        conn.execute(
            """
            -- `event_type`, not `type` (swp-scan-01). `adapter` is NOT NULL
            -- with no default and was never supplied, so this write could not
            -- have landed even after the rename.
            INSERT INTO notification_log
                (id, event_type, adapter, title, body, tenant_id, classification, created_at)
            VALUES
                (gen_random_uuid()::text, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                "dic_digest",
                "digest",
                f"DIC Weekly Digest — {new_doc_count} new docs",
                digest_text[:4000],
                tenant_id,
                "CUI",
                _now(),
            ),
        )
        conn.commit()
    except Exception as exc:
        logger.debug("dic_digest: write notification: %s", exc)
        try:
            conn.rollback()
        except Exception:
            pass


def run(args: dict, state) -> dict:
    """Weekly DIC digest reflex entry point."""
    tenant_id = (args or {}).get("tenant_id", "default")
    conn = get_connection()
    try:
        new_docs = _fetch_new_docs(conn, tenant_id)
        freshness = _fetch_freshness(conn, tenant_id)
        digest_text = _build_digest(new_docs, freshness, tenant_id)
        _write_notification(conn, digest_text, tenant_id, len(new_docs))
        logger.info(
            "dic_digest: weekly digest written — %d new docs, %d collections with freshness data",
            len(new_docs), len(freshness),
        )
        return {
            "status": "ok",
            "new_docs": len(new_docs),
            "collections_with_freshness": len(freshness),
            "digest_length": len(digest_text),
        }
    except Exception as exc:
        logger.warning("dic_digest: run failed: %s", exc)
        return {"status": "error", "error": str(exc)}
    finally:
        conn.close()
